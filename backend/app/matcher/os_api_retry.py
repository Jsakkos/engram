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

from loguru import logger

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
    callable_,
    *args,
    max_attempts: int = 4,
    base_delay: float = 5.0,
    **kwargs,
):
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
    delay = base_delay
    for attempt in range(max_attempts):
        try:
            return callable_(*args, **kwargs)
        except Exception as exc:
            if attempt == max_attempts - 1:
                raise
            retry_after = _parse_retry_after(exc)
            sleep_for = retry_after if retry_after is not None else delay
            is_rate_limit = "429" in str(exc) or retry_after is not None
            source = "Retry-After" if retry_after is not None else "exponential"
            logger.warning(
                f"OS API attempt {attempt + 1}/{max_attempts} failed "
                f"({'rate limited' if is_rate_limit else exc}); "
                f"sleeping {sleep_for:.1f}s ({source})"
            )
            time.sleep(sleep_for)
            if retry_after is None:
                delay *= 2
