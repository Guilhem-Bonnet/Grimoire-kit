"""Grimoire Kit — Composable AI agent platform.

Re-exports are resolved lazily (PEP 562). Importing ``grimoire`` used to pull
``core.config``, ``core.project`` and their dependency graph — 52 ms measured —
which every entry point paid whether it touched them or not, the lifecycle
hooks included. Those run once per tool call, so a convenience import there is
a tax on every action of every session.

``from grimoire import GrimoireConfig`` behaves exactly as before; the cost is
now paid on first use rather than on import.
"""

from typing import TYPE_CHECKING, Any

from grimoire.__version__ import __version__

if TYPE_CHECKING:  # names remain statically visible to type checkers
    from grimoire.core.config import GrimoireConfig
    from grimoire.core.exceptions import GrimoireError
    from grimoire.core.project import GrimoireProject

__all__ = ["GrimoireConfig", "GrimoireError", "GrimoireProject", "__version__"]

_LAZY = {
    "GrimoireConfig": ("grimoire.core.config", "GrimoireConfig"),
    "GrimoireError": ("grimoire.core.exceptions", "GrimoireError"),
    "GrimoireProject": ("grimoire.core.project", "GrimoireProject"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(target[0]), target[1])


def __dir__() -> list[str]:
    return sorted(__all__)
