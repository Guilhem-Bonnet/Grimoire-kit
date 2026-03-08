"""Base class and helpers for Grimoire tools.

Every SDK-side tool inherits from :class:`GrimoireTool` and exposes a typed
``run()`` method.  Standalone CLI wrappers live in each tool module's
``if __name__`` block.

Helpers
-------
- :func:`find_project_root` — walk up to ``project-context.yaml``
- :func:`load_yaml` — safe YAML loader (ruamel → PyYAML fallback)
- :func:`save_yaml` — write YAML preserving comments (ruamel)
- :func:`estimate_tokens` — rough GPT-style token estimate
"""

from __future__ import annotations

import abc
from pathlib import Path
from typing import Any

# ── Helpers ───────────────────────────────────────────────────────────────────

def find_project_root(start: Path | None = None) -> Path:
    """Walk up to find the directory containing ``project-context.yaml``.

    Raises :class:`FileNotFoundError` if none is found.
    """
    current = (start or Path.cwd()).resolve()
    for parent in [current, *current.parents]:
        if (parent / "project-context.yaml").is_file():
            return parent
    msg = f"No project-context.yaml found from {current}"
    raise FileNotFoundError(msg)


def _get_yaml_loader() -> tuple[Any, str]:
    """Return (module, backend_name) for the best available YAML library."""
    try:
        from ruamel.yaml import YAML  # noqa: F811

        return YAML, "ruamel"
    except ImportError:
        pass
    try:
        import yaml  # type: ignore[import-untyped]

        return yaml, "pyyaml"
    except ImportError:
        pass
    msg = "No YAML library found. Install ruamel.yaml or PyYAML."
    raise ImportError(msg)


def load_yaml(path: Path) -> Any:
    """Load a YAML file using ruamel.yaml (safe), falling back to PyYAML."""
    loader, backend = _get_yaml_loader()
    try:
        if backend == "ruamel":
            yaml = loader(typ="safe")
            return yaml.load(path)
        with open(path) as fh:
            return loader.safe_load(fh)
    except Exception as exc:
        raise OSError(f"Cannot parse YAML '{path}': {exc}") from exc


def save_yaml(data: Any, path: Path) -> None:
    """Write *data* to a YAML file, preserving comments when possible."""
    loader, backend = _get_yaml_loader()
    if backend == "ruamel":
        yaml = loader()
        yaml.default_flow_style = False
        yaml.width = 120
        with open(path, "w") as fh:
            yaml.dump(data, fh)
    else:
        with open(path, "w") as fh:
            loader.dump(data, fh, default_flow_style=False, allow_unicode=True, width=120)


_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Rough token estimate (≈ 1 token per 4 chars, GPT-style)."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


# ── Base Class ────────────────────────────────────────────────────────────────

class GrimoireTool(abc.ABC):
    """Abstract base for all SDK-side Grimoire tools.

    Subclasses must implement :meth:`run` which returns a typed result
    dataclass.  The tool receives the resolved *project_root* at init
    and can use the helpers above.
    """

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root.resolve()

    @property
    def project_root(self) -> Path:
        return self._project_root

    @abc.abstractmethod
    def run(self, **kwargs: Any) -> Any:
        """Execute the tool and return a structured result."""
        ...
