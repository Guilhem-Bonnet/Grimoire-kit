"""Retry decorator with exponential backoff for Grimoire Kit."""

from __future__ import annotations

import functools
import logging
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

__all__ = ["with_retry"]

_T = TypeVar("_T")
_logger = logging.getLogger(__name__)


def with_retry(
    *,
    max_attempts: int = 3,
    initial_delay: float = 0.5,
    max_delay: float = 30.0,
    backoff: float = 2.0,
    jitter: bool = True,
    retryable: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError, OSError),
) -> Callable[[Callable[..., _T]], Callable[..., _T]]:
    """Retry a callable with exponential backoff.

    Parameters
    ----------
    max_attempts:
        Total number of attempts (including the first call).
    initial_delay:
        Delay in seconds before the first retry.
    max_delay:
        Upper bound for the delay between retries.
    backoff:
        Multiplier applied to the delay after each failure.
    jitter:
        Randomise the delay (±25 %) to avoid thundering-herd effects.
    retryable:
        Exception types that trigger a retry.  All others propagate
        immediately.

    Example::

        from grimoire.core.retry import with_retry

        @with_retry(max_attempts=3, retryable=(ConnectionError,))
        def fetch(url: str) -> bytes: ...
    """

    def decorator(func: Callable[..., _T]) -> Callable[..., _T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> _T:
            delay = initial_delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retryable:
                    if attempt >= max_attempts:
                        raise
                    actual = min(delay, max_delay)
                    if jitter:
                        actual *= random.uniform(0.75, 1.25)  # noqa: S311
                    _logger.warning(
                        "%s attempt %d/%d failed — retrying in %.1fs",
                        func.__qualname__,
                        attempt,
                        max_attempts,
                        actual,
                    )
                    time.sleep(actual)
                    delay *= backoff
            msg = "unreachable"
            raise RuntimeError(msg)  # pragma: no cover

        return wrapper

    return decorator
