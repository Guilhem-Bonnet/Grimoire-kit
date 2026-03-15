"""Plugin discovery via ``importlib.metadata`` entry points.

Third-party packages can register tools or backends by declaring
entry points in their ``pyproject.toml``::

    [project.entry-points."grimoire.tools"]
    my_tool = "my_package.tools:MyTool"

    [project.entry-points."grimoire.backends"]
    my_backend = "my_package.backends:MyBackend"

Then :func:`discover_tools` / :func:`discover_backends` will
pick them up automatically at runtime.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import Any

__all__ = ["discover_backends", "discover_tools"]

_log = logging.getLogger(__name__)

_GROUP_TOOLS = "grimoire.tools"
_GROUP_BACKENDS = "grimoire.backends"


def _discover(group: str) -> dict[str, Any]:
    """Load all entry points for *group*, returning ``{name: object}``."""
    eps = entry_points(group=group)
    plugins: dict[str, Any] = {}
    for ep in eps:
        try:
            plugins[ep.name] = ep.load()
        except Exception:
            _log.warning("Failed to load plugin %s from group %s", ep.name, group)
    return plugins


def discover_tools() -> dict[str, Any]:
    """Return all registered ``grimoire.tools`` plugins."""
    return _discover(_GROUP_TOOLS)


def discover_backends() -> dict[str, Any]:
    """Return all registered ``grimoire.backends`` plugins."""
    return _discover(_GROUP_BACKENDS)
