"""Error handling framework for Engram.

Provides custom exception types and decorators for standardized error handling
across the application.
"""

import logging
from functools import wraps

logger = logging.getLogger(__name__)


# Custom Exception Hierarchy
class EngramError(Exception):
    """Base exception for all Engram-specific errors."""

    pass


class MakeMKVError(EngramError):
    """MakeMKV operation failed.

    Raised when MakeMKV CLI operations fail (scanning, ripping, parsing output).
    """

    pass


class MatchingError(EngramError):
    """Episode matching failed.

    Raised when audio fingerprinting or subtitle matching fails.
    """

    pass


class ConfigurationError(EngramError):
    """Configuration validation failed.

    Raised when user configuration is invalid or incomplete.
    """

    pass


class OrganizationError(EngramError):
    """File organization failed.

    Raised when moving/renaming files to library fails.
    """

    pass


class SubtitleError(EngramError):
    """Subtitle download or processing failed.

    Raised when subtitle operations fail (download, parsing, caching).
    """

    pass


class DatabaseError(EngramError):
    """Database operation failed.

    Raised when SQLite operations fail unexpectedly.
    """

    pass


# Error Handling Decorator
def handle_errors(
    *,
    error_types: tuple[type[Exception], ...],
    default_message: str,
    log_level: str = "error",
    reraise: bool = True,
    wrap_as: type[EngramError] | None = None,
):
    """Decorator for standardized error handling.

    Args:
        error_types: Tuple of exception types to catch
        default_message: Message to log when error occurs
        log_level: Logging level (error, warning, info, debug)
        reraise: Whether to re-raise the exception after logging
        wrap_as: Optionally wrap the caught exception in an EngramError subclass

    Example:
        @handle_errors(
            error_types=(subprocess.SubprocessError,),
            default_message="MakeMKV operation failed",
            wrap_as=MakeMKVError
        )
        async def rip_disc():
            # ... operation ...
    """

    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except error_types as e:
                log_func = getattr(logger, log_level)
                log_func(
                    f"{default_message}: {e}",
                    exc_info=(log_level == "error"),
                )
                if wrap_as:
                    raise wrap_as(f"{default_message}: {e}") from e
                if reraise:
                    raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except error_types as e:
                log_func = getattr(logger, log_level)
                log_func(
                    f"{default_message}: {e}",
                    exc_info=(log_level == "error"),
                )
                if wrap_as:
                    raise wrap_as(f"{default_message}: {e}") from e
                if reraise:
                    raise

        # Return appropriate wrapper based on function type
        import inspect

        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


# Context Manager for Error Handling
class error_context:
    """Context manager for error handling in specific code blocks.

    Example:
        with error_context(
            error_types=(FileNotFoundError, PermissionError),
            default_message="Failed to read file",
            wrap_as=ConfigurationError
        ):
            # ... code that might raise errors ...
    """

    def __init__(
        self,
        *,
        error_types: tuple[type[Exception], ...],
        default_message: str,
        log_level: str = "error",
        wrap_as: type[EngramError] | None = None,
    ):
        self.error_types = error_types
        self.default_message = default_message
        self.log_level = log_level
        self.wrap_as = wrap_as

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None and issubclass(exc_type, self.error_types):
            log_func = getattr(logger, self.log_level)
            log_func(
                f"{self.default_message}: {exc_val}",
                exc_info=(self.log_level == "error"),
            )
            if self.wrap_as:
                raise self.wrap_as(f"{self.default_message}: {exc_val}") from exc_val
            return False  # Re-raise the original exception
        return False  # Don't suppress other exceptions
