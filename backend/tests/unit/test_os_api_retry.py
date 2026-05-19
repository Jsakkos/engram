"""Unit tests for the unified OpenSubtitles retry helper.

Validates the contract that all three OS API call sites
(login/search/download) depend on: Retry-After header honored when present,
capped exponential fallback when absent, max-attempts re-raises, and the
cap clamps a runaway header value.
"""

from unittest.mock import Mock, patch

import pytest

from app.matcher.os_api_retry import _RETRY_AFTER_CAP_SECONDS, _parse_retry_after, os_api_call


@pytest.mark.unit
class TestOsApiCall:
    """Behavioral contract for ``os_api_call``."""

    def test_returns_callable_result_on_first_success(self):
        """Happy path: no retry, just return what the callable returned."""
        callable_ = Mock(return_value="hello")
        result = os_api_call(callable_, "arg", kw="kw")
        assert result == "hello"
        callable_.assert_called_once_with("arg", kw="kw")

    def test_honors_retry_after_header_when_present(self):
        """When the exception carries a Retry-After header, sleep for that long
        (clamped) and skip the exponential schedule."""

        class FakeHTTPError(Exception):
            pass

        first_exc = FakeHTTPError("429 rate limited")
        first_exc.response = Mock()
        first_exc.response.headers = {"Retry-After": "7"}

        callable_ = Mock(side_effect=[first_exc, "ok"])

        with patch("app.matcher.os_api_retry.time.sleep") as sleep:
            result = os_api_call(callable_, max_attempts=4, base_delay=5.0)

        assert result == "ok"
        # Server said wait 7s — we honored it instead of using base_delay=5.
        sleep.assert_called_once_with(7.0)

    def test_exponential_fallback_when_no_retry_after(self):
        """No Retry-After (the normal case with the current library) →
        capped exponential 5, 10, 20, ..."""
        callable_ = Mock(
            side_effect=[
                RuntimeError("transient 1"),
                RuntimeError("transient 2"),
                RuntimeError("transient 3"),
                "ok",
            ]
        )
        with patch("app.matcher.os_api_retry.time.sleep") as sleep:
            result = os_api_call(callable_, max_attempts=4, base_delay=5.0)

        assert result == "ok"
        # 3 failures means 3 sleeps: 5s, 10s, 20s. The 4th attempt succeeds.
        assert [call.args[0] for call in sleep.call_args_list] == [5.0, 10.0, 20.0]

    def test_reraises_after_max_attempts(self):
        """After exhausting attempts the original exception bubbles up so
        callers (e.g., _get_os_client) can latch the failure state."""
        boom = RuntimeError("persistent failure")
        callable_ = Mock(side_effect=boom)

        with patch("app.matcher.os_api_retry.time.sleep"):
            with pytest.raises(RuntimeError, match="persistent failure"):
                os_api_call(callable_, max_attempts=3, base_delay=1.0)
        assert callable_.call_count == 3

    def test_retry_after_capped_at_300s(self):
        """A bogus header like ``Retry-After: 99999`` must not hang a build."""

        class FakeHTTPError(Exception):
            pass

        bad = FakeHTTPError("429")
        bad.response = Mock()
        bad.response.headers = {"Retry-After": "99999"}

        callable_ = Mock(side_effect=[bad, "ok"])

        with patch("app.matcher.os_api_retry.time.sleep") as sleep:
            os_api_call(callable_, max_attempts=4, base_delay=5.0)

        sleep.assert_called_once_with(_RETRY_AFTER_CAP_SECONDS)

    def test_retry_after_nonnumeric_falls_back_to_exponential(self):
        """A garbage value in Retry-After must not crash; fall back to the
        exponential schedule."""

        class FakeHTTPError(Exception):
            pass

        bad = FakeHTTPError("429")
        bad.response = Mock()
        bad.response.headers = {"Retry-After": "not-a-number"}

        callable_ = Mock(side_effect=[bad, "ok"])

        with patch("app.matcher.os_api_retry.time.sleep") as sleep:
            os_api_call(callable_, max_attempts=4, base_delay=5.0)

        sleep.assert_called_once_with(5.0)

    def test_no_response_attribute_skips_header_path(self):
        """Today's opensubtitlescom wraps exceptions without preserving the
        response — the helper must still work and use exponential backoff."""
        callable_ = Mock(side_effect=[RuntimeError("bare exception"), "ok"])

        with patch("app.matcher.os_api_retry.time.sleep") as sleep:
            os_api_call(callable_, max_attempts=4, base_delay=5.0)

        sleep.assert_called_once_with(5.0)


@pytest.mark.unit
class TestParseRetryAfter:
    """Direct tests for the header parser, since it has its own failure modes."""

    def test_returns_none_when_no_response(self):
        assert _parse_retry_after(RuntimeError("bare")) is None

    def test_returns_none_when_header_missing(self):
        exc = RuntimeError("429")
        exc.response = Mock()
        exc.response.headers = {}
        assert _parse_retry_after(exc) is None

    def test_clamps_to_cap(self):
        exc = RuntimeError("429")
        exc.response = Mock()
        exc.response.headers = {"Retry-After": "1000000"}
        assert _parse_retry_after(exc) == _RETRY_AFTER_CAP_SECONDS

    def test_parses_float_seconds(self):
        exc = RuntimeError("429")
        exc.response = Mock()
        exc.response.headers = {"Retry-After": "12.5"}
        assert _parse_retry_after(exc) == 12.5
