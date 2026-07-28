"""Error handling framework for Engram.

Provides custom exception types and decorators for standardized error handling
across the application.
"""

import inspect
import logging
from functools import wraps
from typing import Literal

logger = logging.getLogger(__name__)

# The closed set of causes `ai_client.classify_provider_error` can produce.
# Defined here (not in ai_client.py) because AIProviderError.code is typed
# against it and ai_client.py already imports from this module — importing
# in the other direction would be circular.
ProviderErrorCode = Literal[
    "bad_key",
    "no_credits",
    "rate_limited",
    "model_unavailable",
    "bad_request",
    "network",
    "timeout",
    "unknown",
]


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


class AIProviderError(EngramError):
    """LLM provider call failed.

    Raised when an AI provider request fails at the transport/HTTP layer
    (rate-limit exhausted, auth, out-of-credits, provider 5xx, network). Lets
    callers distinguish a provider failure from "the model ran and was not
    confident" (which stays a plain ``None``/no-match result).
    """

    def __init__(
        self,
        message: str,
        *,
        code: ProviderErrorCode = "unknown",
        detail: str = "",
    ) -> None:
        """``code`` is a stable machine-readable cause (see
        ``ai_client.classify_provider_error``); ``detail`` is the provider's own
        truncated explanation, kept for logs. Both default so existing
        single-argument call sites are unaffected.

        ``detail`` holds the provider's response body VERBATIM and is deliberately
        left unsanitized, because it is structured data rather than a log line.
        A provider body is attacker-adjacent (a compromised provider or an
        intercepting proxy can embed CRLF to forge log entries), so any caller
        that writes ``detail`` into a log message must pass it through
        ``app.core.security.sanitize_log_value`` first, the way ``complete_json``
        already does. Do not send it to the client either; the UI is given the
        ``code`` and the composed human message, never the raw body.

        The default deliberately reuses ``"unknown"`` rather than introducing a
        separate "never classified" sentinel: ``classify_provider_error`` already
        returns ``"unknown"`` for its own genuinely-indeterminate verdict (e.g. an
        HTTP 500 with no recognizable body), and in both cases there is nothing
        more specific to tell the user, so a UI branching on ``.code`` renders the
        same generic message either way. Splitting them would add a state
        (``"unset"``) with no distinct UI treatment, and would also break the
        established default in ``test_errors.py::test_defaults_keep_existing_call_sites_working``.
        """
        super().__init__(message)
        self.code = code
        self.detail = detail


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
        def _handle(e: Exception) -> None:
            """Log the caught error and either wrap or re-raise it."""
            log_func = getattr(logger, log_level)
            log_func(
                f"{default_message}: {e}",
                exc_info=(log_level == "error"),
            )
            if wrap_as:
                raise wrap_as(f"{default_message}: {e}") from e
            if reraise:
                raise e

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except error_types as e:
                _handle(e)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except error_types as e:
                _handle(e)

        # Return appropriate wrapper based on function type
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
