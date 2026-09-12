"""Base class and helpers for Grimoire tools.

Every SDK-side tool inherits from :class:`GrimoireTool` and exposes a typed
``run()`` method.  Standalone CLI wrappers live in each tool module's
``if __name__`` block.

Helpers
-------
- :func:`find_project_root` — walk up to ``project-context.yaml``
- :func:`load_yaml` — safe, comment-free YAML loader, read-only (ruamel → PyYAML fallback)
- :func:`load_yaml_roundtrip` — comment/quote/style-preserving loader, for a later :func:`save_yaml`
- :func:`save_yaml` — write YAML preserving comments (ruamel round-trip)
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
        from ruamel.yaml import YAML

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
    """Load a YAML file as plain, comment-free data (safe mode).

    Read-only. The result is a bare ``dict``/``list``/scalar with no
    comment, quote-style or flow/block metadata attached — it must never be
    handed to :func:`save_yaml` to rewrite an existing file, since there
    would be nothing left to round-trip and every comment in that file
    would be silently discarded (grimoire-kit#430). Callers that
    read-modify-write a file must load it with :func:`load_yaml_roundtrip`
    instead.
    """
    loader, backend = _get_yaml_loader()
    try:
        if backend == "ruamel":
            yaml = loader(typ="safe")
            return yaml.load(path)
        with open(path) as fh:
            return loader.safe_load(fh)
    except Exception as exc:
        raise OSError(f"Cannot parse YAML '{path}': {exc}") from exc


def _roundtrip_yaml() -> Any:
    """A ``ruamel.yaml.YAML`` round-trip instance configured to keep a
    rewritten file as close as possible to the original: quoted scalars stay
    quoted, inline (flow-style) collections stay inline, and lines are never
    reflowed to a fixed width.

    Raises :class:`ImportError` if ``ruamel.yaml`` is unavailable — there is
    no comment-preserving PyYAML equivalent, so a caller that must round-trip
    a file has no lossy fallback to silently degrade to.
    """
    from ruamel.yaml import YAML

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 1 << 20  # effectively "no reflow"
    return yaml


def load_yaml_roundtrip(path: Path) -> Any:
    """Load a YAML file preserving comments, quote style, inline collections
    and indentation, so it can later be rewritten with :func:`save_yaml`
    without disturbing anything but the keys actually changed.

    Requires ``ruamel.yaml``.
    """
    try:
        yaml = _roundtrip_yaml()
    except ImportError as exc:
        msg = (
            "load_yaml_roundtrip() requires ruamel.yaml — comment-preserving "
            "round-trip has no PyYAML equivalent"
        )
        raise ImportError(msg) from exc
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.load(fh)
    except Exception as exc:
        raise OSError(f"Cannot parse YAML '{path}': {exc}") from exc


def save_yaml(data: Any, path: Path) -> None:
    """Write *data* to a YAML file, preserving comments/formatting.

    When rewriting an *existing* file, *data* must be the object returned
    by :func:`load_yaml_roundtrip` (a ruamel ``CommentedMap``/``CommentedSeq``),
    mutated in place. A plain ``dict``/``list`` carries no comment or
    formatting metadata to round-trip, so passing one here is refused with
    :class:`TypeError` (``plain_dict_would_lose_comments`` /
    ``plain_list_would_lose_comments``) — this is exactly the bug class of
    grimoire-kit#430 (silent comment loss on ``grimoire upgrade``), caught at
    the call site instead of in review. A plain ``dict``/``list`` remains
    fine for a *brand-new* file that has no comments to lose.
    """
    loader, backend = _get_yaml_loader()
    if backend == "ruamel":
        from ruamel.yaml.comments import CommentedMap, CommentedSeq

        if isinstance(data, dict) and not isinstance(data, CommentedMap):
            msg = (
                "plain_dict_would_lose_comments: save_yaml() refuses a plain "
                "dict — it has no comment/format metadata to round-trip and "
                "would silently strip every comment from an existing file "
                "(grimoire-kit#430). Load the file with "
                "load_yaml_roundtrip() first, mutate that CommentedMap in "
                "place, then pass it to save_yaml()."
            )
            raise TypeError(msg)
        if isinstance(data, list) and not isinstance(data, CommentedSeq):
            msg = (
                "plain_list_would_lose_comments: save_yaml() refuses a plain "
                "list for the same reason a plain dict is refused — see "
                "grimoire-kit#430."
            )
            raise TypeError(msg)
        yaml = _roundtrip_yaml()
        with open(path, "w", encoding="utf-8") as fh:
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
