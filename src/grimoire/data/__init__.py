"""Access bundled data files (framework, templates) at runtime.

Works both from a wheel install and an editable (``pip install -e .``) dev
install by falling back to the repository root when the packaged directory
is absent.
"""

from __future__ import annotations

from pathlib import Path

# Resolved once, cached for the process lifetime.
_framework_cache: Path | None = None
_web_cache: Path | None = None


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


def web_path() -> Path:
    """Return the filesystem path to the bundled cockpit ``web/`` site root."""
    global _web_cache
    if _web_cache is not None:
        return _web_cache

    # 1. Wheel install: hatch force-include copies web → grimoire/data/web/
    pkg = Path(__file__).parent / "web"
    if pkg.is_dir():
        _web_cache = pkg
        return pkg

    # 2. Editable install: traverse up from src/grimoire/data/ → repo root
    dev = Path(__file__).resolve().parent.parent.parent.parent / "web"
    if dev.is_dir():
        _web_cache = dev
        return dev

    msg = (
        "Cannot locate bundled cockpit web/ directory. "
        "If this is an editable install, ensure the repository root "
        "contains the 'web/' directory."
    )
    raise FileNotFoundError(msg)


def site_script(name: str) -> Path:
    """Return the path to a bundled site generation script (e.g. ``gen-site-data.py``)."""
    pkg = Path(__file__).parent / "site-scripts" / name
    if pkg.is_file():
        return pkg
    dev = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / name
    if dev.is_file():
        return dev
    msg = f"Cannot locate bundled site script {name!r}."
    raise FileNotFoundError(msg)
