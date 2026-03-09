"""Bundled archetypes — access built-in archetype data at runtime."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def bundled_path() -> Path:
    """Return the filesystem path to the bundled archetypes directory.

    Works both in development (editable install) and from a wheel.
    """
    ref = files("grimoire.archetypes")
    # importlib.resources guarantees a real path for packages on disk
    return Path(str(ref))
