"""Bundled archetypes — access built-in archetype data at runtime."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

# Resolved once, cached for the process lifetime.
_cache: Path | None = None


def bundled_path() -> Path:
    """Return the filesystem path to the bundled archetypes directory.

    Works both in development (editable install) and from a wheel.
    In a wheel, hatch ``force-include`` copies ``archetypes/`` into the
    ``grimoire/archetypes/`` package.  In dev mode, the actual data lives
    at the repository root under ``archetypes/``.
    """
    global _cache
    if _cache is not None:
        return _cache

    # 1. Wheel install: archetypes are co-located with this __init__.py
    pkg = Path(str(files("grimoire.archetypes")))
    if (pkg / "meta" / "agents").is_dir():
        _cache = pkg
        return pkg

    # 2. Editable install: traverse up from src/grimoire/archetypes/ → repo root
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    dev = repo_root / "archetypes"
    if dev.is_dir() and (dev / "meta" / "agents").is_dir():
        _cache = dev
        return dev

    msg = (
        "Cannot locate bundled archetypes with agent data. "
        "Ensure the repository root contains the 'archetypes/' directory."
    )
    raise FileNotFoundError(msg)
