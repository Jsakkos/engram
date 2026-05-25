"""Unit tests for the error-handling framework (decorator + context manager).

CLAUDE.md documents @handle_errors / error_context as the backbone of error
handling, so this locks in their wrap/reraise/swallow contract.
"""

import pytest

from app.core.errors import (
    ConfigurationError,
    DatabaseError,
    EngramError,
    MakeMKVError,
    MatchingError,
    OrganizationError,
    SubtitleError,
    error_context,
    handle_errors,
)


@pytest.mark.unit
class TestExceptionHierarchy:
    @pytest.mark.parametrize(
        "exc_cls",
        [
            MakeMKVError,
            MatchingError,
            ConfigurationError,
            OrganizationError,
            SubtitleError,
            DatabaseError,
        ],
    )
    def test_subclasses_are_engram_errors(self, exc_cls):
        assert issubclass(exc_cls, EngramError)
        assert isinstance(exc_cls("boom"), EngramError)


@pytest.mark.unit
class TestHandleErrorsSync:
    def test_returns_value_on_success(self):
        @handle_errors(error_types=(ValueError,), default_message="nope")
        def add(a, b):
            return a + b

        assert add(2, 3) == 5

    def test_does_not_catch_unlisted_exception(self):
        @handle_errors(error_types=(ValueError,), default_message="nope")
        def boom():
            raise KeyError("unlisted")

        with pytest.raises(KeyError):
            boom()

    def test_reraises_original_by_default(self):
        @handle_errors(error_types=(ValueError,), default_message="bad input")
        def boom():
            raise ValueError("orig")

        with pytest.raises(ValueError, match="orig"):
            boom()

    def test_wrap_as_wraps_and_chains(self):
        @handle_errors(
            error_types=(ValueError,),
            default_message="bad input",
            wrap_as=ConfigurationError,
        )
        def boom():
            raise ValueError("orig")

        with pytest.raises(ConfigurationError) as exc_info:
            boom()
        assert "bad input: orig" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, ValueError)

    def test_swallows_when_not_reraising_and_no_wrap(self):
        @handle_errors(
            error_types=(ValueError,),
            default_message="ignored",
            reraise=False,
        )
        def boom():
            raise ValueError("orig")

        assert boom() is None

    def test_respects_log_level(self, caplog):
        @handle_errors(
            error_types=(ValueError,),
            default_message="warned",
            log_level="warning",
            reraise=False,
        )
        def boom():
            raise ValueError("orig")

        with caplog.at_level("WARNING"):
            boom()
        assert any("warned: orig" in r.message for r in caplog.records)


@pytest.mark.unit
class TestHandleErrorsAsync:
    async def test_returns_value_on_success(self):
        @handle_errors(error_types=(ValueError,), default_message="nope")
        async def add(a, b):
            return a + b

        assert await add(2, 3) == 5

    async def test_wrap_as_wraps_and_chains(self):
        @handle_errors(
            error_types=(RuntimeError,),
            default_message="rip failed",
            wrap_as=MakeMKVError,
        )
        async def boom():
            raise RuntimeError("orig")

        with pytest.raises(MakeMKVError) as exc_info:
            await boom()
        assert "rip failed: orig" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, RuntimeError)

    async def test_swallows_when_not_reraising(self):
        @handle_errors(
            error_types=(RuntimeError,),
            default_message="ignored",
            reraise=False,
        )
        async def boom():
            raise RuntimeError("orig")

        assert await boom() is None

    async def test_does_not_catch_unlisted(self):
        @handle_errors(error_types=(ValueError,), default_message="nope")
        async def boom():
            raise KeyError("unlisted")

        with pytest.raises(KeyError):
            await boom()


@pytest.mark.unit
class TestErrorContext:
    def test_noop_on_clean_exit(self):
        with error_context(error_types=(ValueError,), default_message="x"):
            value = 1 + 1
        assert value == 2

    def test_reraises_when_no_wrap(self):
        with pytest.raises(ValueError, match="orig"):
            with error_context(error_types=(ValueError,), default_message="failed"):
                raise ValueError("orig")

    def test_wrap_as_wraps_and_chains(self):
        with pytest.raises(OrganizationError) as exc_info:
            with error_context(
                error_types=(FileNotFoundError,),
                default_message="move failed",
                wrap_as=OrganizationError,
            ):
                raise FileNotFoundError("missing")
        assert "move failed: missing" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, FileNotFoundError)

    def test_ignores_unlisted_exception(self):
        with pytest.raises(KeyError):
            with error_context(error_types=(ValueError,), default_message="x"):
                raise KeyError("unlisted")
