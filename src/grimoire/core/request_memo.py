"""Mémo borné à une requête : une surface qui évalue beaucoup de tâches d'un coup
(le panneau Preuves) relirait sinon le board et les politiques à chaque tâche.

Hors bloc :func:`request_memo` rien n'est mémorisé ; jamais de cache entre requêtes.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
from collections.abc import Callable, Iterator
from typing import Any

_MEMO: contextvars.ContextVar[dict[Any, Any] | None] = contextvars.ContextVar("grimoire_request_memo", default=None)


@contextlib.contextmanager
def request_memo() -> Iterator[None]:
    token = _MEMO.set({})
    try:
        yield
    finally:
        _MEMO.reset(token)


def memoized[F: Callable[..., Any]](fn: F) -> F:
    """Mémorise ``fn`` (arguments hachables) pendant le bloc :func:`request_memo` courant."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        memo = _MEMO.get()
        if memo is None:
            return fn(*args, **kwargs)
        key = (fn.__qualname__, args, tuple(sorted(kwargs.items())))
        if key not in memo:
            memo[key] = fn(*args, **kwargs)
        return memo[key]

    return wrapper  # type: ignore[return-value]
