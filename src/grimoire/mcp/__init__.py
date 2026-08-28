"""Grimoire MCP — Model Context Protocol server."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

__all__ = ["main"]

if TYPE_CHECKING:
    from grimoire.mcp.server import main as main


def __getattr__(name: str) -> Any:
    """Résolution paresseuse : importer le paquet ne doit pas démarrer le SDK MCP."""
    if name in __all__:
        return getattr(importlib.import_module("grimoire.mcp.server"), name)
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
