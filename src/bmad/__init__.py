"""BMAD Kit — Composable AI agent platform."""

from bmad.__version__ import __version__
from bmad.core.config import BmadConfig
from bmad.core.exceptions import BmadError

__all__ = ["BmadConfig", "BmadError", "__version__"]
