"""Host adapters: one neutral surface, one emitter per agent host.

See :mod:`grimoire.hosts.surface` for the representation,
:mod:`grimoire.hosts.decisions` for the rules, :mod:`grimoire.hosts.runtime`
for the hook wire protocol, and :mod:`grimoire.hosts.emitters` for rendering.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - type checkers only, no runtime cost
    from grimoire.hosts.capabilities import HostProfile, all_profiles, profile_for, resolve_host
    from grimoire.hosts.surface import ProjectSurface

# ``build_surface`` is deliberately *not* re-exported here. It pulls the
# scaffolder and the archetype resolver, and this package is imported by the
# hook entry point on every tool call — a convenience import that costs 21 ms
# per call is not a convenience. Import it from ``grimoire.hosts.collect``.
__all__ = ["HostProfile", "ProjectSurface", "all_profiles", "profile_for", "resolve_host"]

#: name -> submodule that defines it, resolved on first access (issue #419).
#: This ``__init__.py`` runs before *every* submodule import
#: (``grimoire.hosts.decisions``, ``.capabilities``, ``.runtime``…), so an
#: eager ``from grimoire.hosts.surface import ProjectSurface`` here — the
#: exact shape this package already warns against for ``build_surface`` above
#: — paid for the whole agent/model IR (``AgentSpec``, the Rust-optional
#: fingerprint machinery…) on every single hook call, whether or not anything
#: in the call needed it. Nothing in this codebase actually imports these
#: names from the package root (they are all imported from their owning
#: submodule directly); this stays lazy purely so a hypothetical
#: ``from grimoire.hosts import ProjectSurface`` keeps working.
_OWNER = {
    "HostProfile": "capabilities",
    "all_profiles": "capabilities",
    "profile_for": "capabilities",
    "resolve_host": "capabilities",
    "ProjectSurface": "surface",
}


def __getattr__(name: str) -> Any:
    owner = _OWNER.get(name)
    if owner is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(f"{__name__}.{owner}")
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
