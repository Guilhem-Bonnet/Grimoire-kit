"""Grimoire Kit — Composable AI agent platform."""

from grimoire.__version__ import __version__
from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireError

__all__ = ["GrimoireConfig", "GrimoireError", "__version__"]
