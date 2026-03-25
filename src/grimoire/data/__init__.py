"""Access bundled data files (framework, templates) at runtime.

Works both from a wheel install and an editable (``pip install -e .``) dev
install by falling back to the repository root when the packaged directory
is absent.
"""

from __future__ import annotations

from pathlib import Path

# Resolved once, cached for the process lifetime.
_framework_cache: Path | None = None


def framework_path() -> Path:
    """Return the filesystem path to the bundled ``framework/`` directory."""
    global _framework_cache
    if _framework_cache is not None:
        return _framework_cache

    # 1. Wheel install: hatch force-include copies framework → grimoire/data/framework/
    pkg = Path(__file__).parent / "framework"
    if pkg.is_dir():
        _framework_cache = pkg
        return pkg

    # 2. Editable install: traverse up from src/grimoire/data/ → repo root
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    dev = repo_root / "framework"
    if dev.is_dir():
        _framework_cache = dev
        return dev

    msg = (
        "Cannot locate bundled framework directory. "
        "If this is an editable install, ensure the repository root "
        "contains the 'framework/' directory."
    )
    raise FileNotFoundError(msg)
