"""Deprecation utilities for Grimoire Kit public API."""

from __future__ import annotations

import functools
import warnings
from collections.abc import Callable
from typing import Any

__all__ = ["deprecated"]


def deprecated(
    *,
    reason: str,
    version: str,
    alternative: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark a callable as deprecated.

    Emits :class:`DeprecationWarning` on every call.

    Parameters
    ----------
    reason:
        Human-readable explanation of why the function is deprecated.
    version:
        The version in which the deprecation was introduced.
    alternative:
        Optional replacement function/method name.

    Example::

        @deprecated(reason="Replaced by new_func", version="3.2.0",
                    alternative="new_func")
        def old_func() -> None: ...
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        msg = f"{func.__qualname__}() deprecated since v{version}: {reason}"
        if alternative:
            msg += f" Use {alternative}() instead."

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            warnings.warn(msg, DeprecationWarning, stacklevel=2)
            return func(*args, **kwargs)

        return wrapper

    return decorator
