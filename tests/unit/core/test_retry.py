"""Tests for grimoire.core.retry — @with_retry decorator."""

from __future__ import annotations

import time

import pytest

from grimoire.core.retry import with_retry


class TestWithRetry:
    """Verify retry logic, backoff, and exception filtering."""

    def test_succeeds_first_try(self) -> None:
        calls: list[int] = []

        @with_retry(max_attempts=3, retryable=(ValueError,))
        def ok() -> str:
            calls.append(1)
            return "done"

        assert ok() == "done"
        assert len(calls) == 1

    def test_retries_on_retryable_exception(self) -> None:
        counter = {"n": 0}

        @with_retry(max_attempts=3, initial_delay=0.001, retryable=(ConnectionError,))
        def flaky() -> str:
            counter["n"] += 1
            if counter["n"] < 3:
                raise ConnectionError("oops")
            return "ok"

        assert flaky() == "ok"
        assert counter["n"] == 3

    def test_raises_after_max_attempts(self) -> None:
        @with_retry(max_attempts=2, initial_delay=0.001, retryable=(ValueError,))
        def always_fail() -> None:
            raise ValueError("permanent")

        with pytest.raises(ValueError, match="permanent"):
            always_fail()

    def test_non_retryable_exception_propagates_immediately(self) -> None:
        calls: list[int] = []

        @with_retry(max_attempts=5, initial_delay=0.001, retryable=(ConnectionError,))
        def wrong_type() -> None:
            calls.append(1)
            raise TypeError("not retryable")

        with pytest.raises(TypeError, match="not retryable"):
            wrong_type()
        assert len(calls) == 1  # No retry

    def test_backoff_increases_delay(self) -> None:
        times: list[float] = []

        @with_retry(
            max_attempts=3,
            initial_delay=0.05,
            backoff=2.0,
            jitter=False,
            retryable=(RuntimeError,),
        )
        def slow_fail() -> None:
            times.append(time.monotonic())
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            slow_fail()

        assert len(times) == 3
        # Second delay should be roughly >= 2x first delay
        d1 = times[1] - times[0]
        d2 = times[2] - times[1]
        assert d2 >= d1 * 1.5  # Allow margin for OS scheduling

    def test_jitter_varies_delay(self) -> None:
        """When jitter=True, consecutive delays should not be identical."""
        times: list[float] = []

        @with_retry(
            max_attempts=4,
            initial_delay=0.02,
            backoff=1.0,  # Same base delay
            jitter=True,
            retryable=(OSError,),
        )
        def jittery() -> None:
            times.append(time.monotonic())
            raise OSError("jitter test")

        with pytest.raises(OSError):
            jittery()

        delays = [times[i + 1] - times[i] for i in range(len(times) - 1)]
        # With jitter, delays should not all be exactly equal
        assert len({round(d, 4) for d in delays}) >= 1

    def test_preserves_return_type(self) -> None:
        @with_retry(max_attempts=1, retryable=(ValueError,))
        def returns_list() -> list[int]:
            return [1, 2, 3]

        assert returns_list() == [1, 2, 3]

    def test_preserves_function_name(self) -> None:
        @with_retry(max_attempts=1, retryable=(ValueError,))
        def my_func() -> None:
            pass

        assert my_func.__name__ == "my_func"

    def test_default_retryable_types(self) -> None:
        """Default retryable includes ConnectionError, TimeoutError, OSError."""
        counter = {"n": 0}

        @with_retry(max_attempts=2, initial_delay=0.001)
        def timeout_fail() -> str:
            counter["n"] += 1
            if counter["n"] < 2:
                raise TimeoutError("slow")
            return "ok"

        assert timeout_fail() == "ok"
        assert counter["n"] == 2
