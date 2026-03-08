"""Local registry — catalog of built-in agents and archetypes.

Indexes all agents from the ``archetypes/`` directory and provides
search, listing, and inspection capabilities.

Usage::

    from bmad.registry.local import LocalRegistry

    reg = LocalRegistry(kit_root=Path("bmad-custom-kit"))
    for item in reg.list_agents():
        print(f"{item.id} — {item.archetype}")
    results = reg.search("kubernetes")
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bmad.core.exceptions import BmadRegistryError
from bmad.registry.agents import AgentRegistry, ArchetypeDNA

# ── Data models ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RegistryItem:
    """A single item (agent) in the registry."""

    id: str
    archetype: str
    description: str
    path: Path
    required: bool = True
    tags: tuple[str, ...] = ()


# ── Registry ──────────────────────────────────────────────────────────────────


class LocalRegistry:
    """Read-only catalog of built-in archetypes and agents.

    Wraps :class:`AgentRegistry` with higher-level search and listing.
    """

    def __init__(self, kit_root: Path) -> None:
        self._registry = AgentRegistry(kit_root)
        self._kit_root = kit_root.resolve()
        self._index: list[RegistryItem] | None = None

    def _build_index(self) -> list[RegistryItem]:
        """Lazily build the full agent index."""
        if self._index is not None:
            return self._index

        items: list[RegistryItem] = []
        for arch_id in self._registry.list_archetypes():
            try:
                dna = self._registry.get_dna(arch_id)
            except BmadRegistryError:
                continue
            for agent in dna.agents:
                items.append(RegistryItem(
                    id=agent.id,
                    archetype=arch_id,
                    description=agent.description,
                    path=agent.path,
                    required=agent.required,
                    tags=dna.tags,
                ))
        self._index = items
        return items

    def list_archetypes(self) -> list[str]:
        """Return sorted list of archetype IDs."""
        return self._registry.list_archetypes()

    def list_agents(self) -> list[RegistryItem]:
        """Return all agents across all archetypes."""
        return list(self._build_index())

    def get(self, agent_id: str) -> RegistryItem:
        """Look up a specific agent by ID.

        Returns the first match.  Raises :class:`BmadRegistryError`
        if not found.
        """
        for item in self._build_index():
            if item.id == agent_id:
                return item
        raise BmadRegistryError(
            f"Agent '{agent_id}' not found in local registry."
        )

    def search(self, query: str) -> list[RegistryItem]:
        """Search agents by keyword in id, archetype, description, and tags."""
        q = query.lower()
        results: list[RegistryItem] = []
        for item in self._build_index():
            haystack = " ".join([
                item.id,
                item.archetype,
                item.description,
                " ".join(item.tags),
            ]).lower()
            if q in haystack:
                results.append(item)
        return results

    def inspect_archetype(self, archetype_id: str) -> ArchetypeDNA:
        """Return the full DNA for an archetype.

        Raises :class:`BmadRegistryError` if not found.
        """
        return self._registry.get_dna(archetype_id)
