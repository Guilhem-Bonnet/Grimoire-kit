"""Grimoire core — business logic and domain models.

Re-exports are resolved lazily (PEP 562). Eagerly importing the eleven names
below meant that touching *anything* under ``grimoire.core`` — reading a task
id, resolving a profile path — pulled the scaffolder, the archetype resolver
and the stack scanner with it. The lifecycle hooks read exactly those two
facts on every tool call, and paid the whole graph for them.

``from grimoire.core import ProjectScaffolder`` behaves as before. Attribute
access to a submodule (``grimoire.core.config``) also still resolves, so code
relying on the previous import side effect keeps working.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # names remain statically visible to type checkers
    from grimoire.core.archetype_resolver import ArchetypeResolver
    from grimoire.core.config import GrimoireConfig
    from grimoire.core.deprecation import deprecated
    from grimoire.core.exceptions import GrimoireError
    from grimoire.core.log import configure_logging
    from grimoire.core.project import GrimoireProject
    from grimoire.core.resolver import PathResolver
    from grimoire.core.retry import with_retry
    from grimoire.core.scaffold import ProjectScaffolder
    from grimoire.core.scanner import StackScanner
    from grimoire.core.schema import generate_schema

__all__ = [
    "ArchetypeResolver",
    "GrimoireConfig",
    "GrimoireError",
    "GrimoireProject",
    "PathResolver",
    "ProjectScaffolder",
    "StackScanner",
    "configure_logging",
    "deprecated",
    "generate_schema",
    "with_retry",
]

_LAZY: dict[str, tuple[str, str]] = {
    "ArchetypeResolver": ("grimoire.core.archetype_resolver", "ArchetypeResolver"),
    "GrimoireConfig": ("grimoire.core.config", "GrimoireConfig"),
    "GrimoireError": ("grimoire.core.exceptions", "GrimoireError"),
    "GrimoireProject": ("grimoire.core.project", "GrimoireProject"),
    "PathResolver": ("grimoire.core.resolver", "PathResolver"),
    "ProjectScaffolder": ("grimoire.core.scaffold", "ProjectScaffolder"),
    "StackScanner": ("grimoire.core.scanner", "StackScanner"),
    "configure_logging": ("grimoire.core.log", "configure_logging"),
    "deprecated": ("grimoire.core.deprecation", "deprecated"),
    "generate_schema": ("grimoire.core.schema", "generate_schema"),
    "with_retry": ("grimoire.core.retry", "with_retry"),
}


def __getattr__(name: str) -> Any:
    import importlib

    target = _LAZY.get(name)
    if target is not None:
        return getattr(importlib.import_module(target[0]), target[1])
    # Submodule access (``grimoire.core.config``) used to work as a side effect
    # of the eager imports; keep it working rather than trading one breakage
    # for a speed-up.
    try:
        return importlib.import_module(f"{__name__}.{name}")
    except ImportError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


def __dir__() -> list[str]:
    return sorted(__all__)
