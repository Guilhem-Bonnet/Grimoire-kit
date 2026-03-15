"""Grimoire Kit — Composable AI agent platform."""

from grimoire.__version__ import __version__
from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireError
from grimoire.core.project import GrimoireProject

__all__ = ["GrimoireConfig", "GrimoireError", "GrimoireProject", "__version__"]
