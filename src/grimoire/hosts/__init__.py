"""Host adapters: one neutral surface, one emitter per agent host.

See :mod:`grimoire.hosts.surface` for the representation,
:mod:`grimoire.hosts.decisions` for the rules, :mod:`grimoire.hosts.runtime`
for the hook wire protocol, and :mod:`grimoire.hosts.emitters` for rendering.
"""

from __future__ import annotations

from grimoire.hosts.capabilities import HostProfile, all_profiles, profile_for, resolve_host
from grimoire.hosts.surface import ProjectSurface

# ``build_surface`` is deliberately *not* re-exported here. It pulls the
# scaffolder and the archetype resolver, and this package is imported by the
# hook entry point on every tool call — a convenience import that costs 21 ms
# per call is not a convenience. Import it from ``grimoire.hosts.collect``.
__all__ = ["HostProfile", "ProjectSurface", "all_profiles", "profile_for", "resolve_host"]
