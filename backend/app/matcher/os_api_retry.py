"""Unified retry/backoff helper for opensubtitlescom calls.

The OpenSubtitles best-practices doc requires honoring the ``Retry-After``
response header on 429 responses. Unfortunately the installed library
(``opensubtitlescom``) wraps ``requests.HTTPError`` into a bare
``OpenSubtitlesException(str(http_err))`` in ``send_api`` and discards the
response object, so we cannot read the header in practice today.

This helper:

1. Honors ``Retry-After`` defensively via ``getattr(exc, "response", None)``,
   in case a future library version preserves the response (then we
   automatically benefit with no further changes).
2. Falls back to capped exponential backoff (5s, 10s, 20s, 40s by default)
   when the header is not available — which is the current real-world path.
3. Detects rate limiting via substring match on the exception (``"429" in
   str(exc)``), matching what the rest of the codebase does, so the warning
   log lines correctly identify rate limits vs. transient network errors.

All three OpenSubtitles call sites (``client.login``, ``client.search``,
``client.download_and_save``) route through this helper for consistent
behavior — before this helper, login used an inline loop in
``testing_service.py`` while search/download used a generic
``retry_with_backoff`` decorator in ``subtitle_provider.py`` with different
parameters.
"""

import time
from collections.abc import Callable
from typing import TypeVar

import requests
from loguru import logger

_T = TypeVar("_T")

try:
    from opensubtitlescom.exceptions import OpenSubtitlesException
except ImportError:
    # Library not installed in this environment; define a stand-in so the
    # retry tuple still type-checks. The actual call sites guard against
    # ImportError at a higher level (see testing_service._get_os_client).
    class OpenSubtitlesException(Exception):  # type: ignore[no-redef]
        pass


# Catch only the failure modes that are actually retryable. Catching bare
# Exception would silently retry programming bugs (AttributeError,
# TypeError) inside the callable — those should surface immediately.
_RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    OpenSubtitlesException,
    requests.RequestException,
    OSError,
    TimeoutError,
)

# Cap any single sleep at 5 minutes. Protects against a misbehaving server
# (or a stray ``Retry-After: 999999``) hanging a long build run.
_RETRY_AFTER_CAP_SECONDS = 300.0


def _parse_retry_after(exc: Exception) -> float | None:
    """Extract Retry-After (seconds) from an exception's response, if present.

    Returns the clamped delay in seconds, or None if the exception does not
    carry a usable response/header (the common case with the current
    ``opensubtitlescom`` release — see module docstring).
    """
    response = getattr(exc, "response", None)
    if response is None or not hasattr(response, "headers"):
        return None
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return min(float(raw), _RETRY_AFTER_CAP_SECONDS)
    except (TypeError, ValueError):
        return None


def os_api_call(
    callable_: Callable[..., _T],
    *args,
    max_attempts: int = 4,
    base_delay: float = 5.0,
    **kwargs,
) -> _T:
    """Invoke an OpenSubtitles API method with consistent 429-aware backoff.

    Args:
        callable_: An ``opensubtitlescom.OpenSubtitles`` bound method
            (e.g. ``client.login``, ``client.search``, ``client.download_and_save``).
        *args, **kwargs: Forwarded to ``callable_``.
        max_attempts: Total attempts including the first. Default 4.
        base_delay: Initial backoff in seconds for the exponential fallback.
            Doubles each subsequent attempt. Default 5.0.

    Returns:
        Whatever ``callable_`` returns on success.

    Raises:
        The original exception, after the final attempt has exhausted.
    """
    # Precondition check at function entry — surfaces a bad caller
    # (e.g., ``max_attempts=0``) immediately, before any argument-marshalling.
    if max_attempts < 1:
        raise ValueError(f"os_api_call: max_attempts must be >= 1, got {max_attempts}")

    # Structure: do (max_attempts - 1) retry-with-sleep attempts, then ONE
    # final attempt outside the loop. The final attempt either returns its
    # result or lets the exception bubble — neither requires a sentinel
    # return at the bottom, so static analyzers don't flag a mixed-returns
    # fall-through.
    delay = base_delay
    for attempt in range(max_attempts - 1):
        try:
            return callable_(*args, **kwargs)
        except _RETRYABLE_EXCEPTIONS as exc:
            retry_after = _parse_retry_after(exc)
            sleep_for = retry_after if retry_after is not None else delay
            is_rate_limit = "429" in str(exc) or retry_after is not None
            source = "Retry-After" if retry_after is not None else "exponential"
            # CLAUDE.md normally requires ``exc_info=True`` inside except
            # blocks, but for expected 429 responses the rate-limit case is
            # fully described by the log message — adding a stack frame for
            # every retry makes long build logs unreadable. This is a
            # conscious, documented deviation; keep it.
            logger.warning(
                f"OS API attempt {attempt + 1}/{max_attempts} failed "
                f"({'rate limited' if is_rate_limit else exc}); "
                f"sleeping {sleep_for:.1f}s ({source})",
                exc_info=not is_rate_limit,
            )
            time.sleep(sleep_for)
            if retry_after is None:
                delay *= 2

    # Final attempt: succeed or raise. No sleep follows; no fall-through.
    return callable_(*args, **kwargs)
