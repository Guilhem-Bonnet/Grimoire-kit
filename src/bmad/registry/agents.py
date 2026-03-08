"""Agent registry — load and resolve agents from archetypes.

Locates archetype directories, parses DNA files, and provides
access to agent definitions by id or archetype.

Usage::

    from bmad.registry.agents import AgentRegistry

    reg = AgentRegistry(kit_root=Path("."))
    agent = reg.get_agent("minimal", "project-navigator")
    print(agent.description)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bmad.core.exceptions import BmadAgentError, BmadRegistryError

# ── Data models ───────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class AgentDef:
    """A resolved agent definition from an archetype DNA."""

    id: str
    path: Path
    required: bool = True
    description: str = ""

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def load_persona(self) -> str:
        """Read the agent's markdown persona file."""
        if not self.path.is_file():
            raise BmadAgentError(f"Agent file not found: {self.path}")
        return self.path.read_text(encoding="utf-8")


@dataclass(frozen=True, slots=True)
class ArchetypeDNA:
    """Parsed archetype DNA (metadata + agents)."""

    id: str
    name: str
    version: str
    description: str
    root: Path
    agents: tuple[AgentDef, ...]
    inherits: str | None = None
    compatible_with: tuple[str, ...] = ()
    incompatible_with: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any], dna_path: Path) -> ArchetypeDNA:
        """Parse a DNA dict, resolving agent paths relative to the DNA file."""
        dna_dir = dna_path.parent

        raw_agents = data.get("agents") or []
        agents: list[AgentDef] = []
        for entry in raw_agents:
            rel = entry.get("path", "")
            resolved = (dna_dir / rel).resolve()
            agent_id = resolved.stem
            agents.append(AgentDef(
                id=agent_id,
                path=resolved,
                required=bool(entry.get("required", True)),
                description=str(entry.get("description", "")),
            ))

        known = {"id", "name", "version", "description", "agents", "inherits",
                 "compatible_with", "incompatible_with", "tags", "$schema",
                 "icon", "author", "traits", "constraints", "acceptance_criteria",
                 "workflows", "shared_context_template", "prompts_directory",
                 "requires", "auto_detect"}
        extra = {k: v for k, v in data.items() if k not in known}

        return cls(
            id=str(data.get("id", dna_dir.name)),
            name=str(data.get("name", "")),
            version=str(data.get("version", "0.0.0")),
            description=str(data.get("description", "")),
            root=dna_dir,
            agents=tuple(agents),
            inherits=data.get("inherits"),
            compatible_with=tuple(str(s) for s in (data.get("compatible_with") or [])),
            incompatible_with=tuple(str(s) for s in (data.get("incompatible_with") or [])),
            tags=tuple(str(s) for s in (data.get("tags") or [])),
            extra=extra,
        )

    @classmethod
    def from_yaml(cls, dna_path: Path) -> ArchetypeDNA:
        """Load DNA from a YAML file."""
        if not dna_path.is_file():
            raise BmadRegistryError(f"DNA file not found: {dna_path}")
        try:
            from ruamel.yaml import YAML
            yaml = YAML(typ="safe")
            raw = yaml.load(dna_path)
        except ImportError:
            try:
                import yaml as pyyaml  # type: ignore[import-untyped]
                with open(dna_path) as fh:
                    raw = pyyaml.safe_load(fh)
            except Exception as exc:
                raise BmadRegistryError(f"Cannot parse DNA '{dna_path}': {exc}") from exc
        except Exception as exc:
            raise BmadRegistryError(f"Cannot parse DNA '{dna_path}': {exc}") from exc

        if not isinstance(raw, dict):
            raise BmadRegistryError(f"DNA file is not a YAML mapping: {dna_path}")
        return cls.from_dict(raw, dna_path)


# ── Registry ──────────────────────────────────────────────────────────────────

class AgentRegistry:
    """Registry of archetypes and their agents.

    Scans the ``archetypes/`` directory under *kit_root* and lazily
    loads DNA files on first access.
    """

    def __init__(self, kit_root: Path) -> None:
        self._kit_root = kit_root.resolve()
        self._archetypes_dir = self._kit_root / "archetypes"
        self._cache: dict[str, ArchetypeDNA] = {}

    @property
    def archetypes_dir(self) -> Path:
        return self._archetypes_dir

    def list_archetypes(self) -> list[str]:
        """Return sorted list of available archetype IDs."""
        if not self._archetypes_dir.is_dir():
            return []
        return sorted(
            d.name for d in self._archetypes_dir.iterdir()
            if d.is_dir() and (d / "archetype.dna.yaml").is_file()
        )

    def get_dna(self, archetype_id: str) -> ArchetypeDNA:
        """Load and return the DNA for an archetype (cached)."""
        if archetype_id in self._cache:
            return self._cache[archetype_id]

        dna_path = self._archetypes_dir / archetype_id / "archetype.dna.yaml"
        if not dna_path.is_file():
            available = self.list_archetypes()
            raise BmadRegistryError(
                f"Archetype '{archetype_id}' not found. "
                f"Available: {available}"
            )

        dna = ArchetypeDNA.from_yaml(dna_path)
        self._cache[archetype_id] = dna
        return dna

    def get_agent(self, archetype_id: str, agent_id: str) -> AgentDef:
        """Get a specific agent from an archetype."""
        dna = self.get_dna(archetype_id)
        for agent in dna.agents:
            if agent.id == agent_id:
                return agent
        available = [a.id for a in dna.agents]
        raise BmadAgentError(
            f"Agent '{agent_id}' not found in archetype '{archetype_id}'. "
            f"Available: {available}"
        )

    def resolve_agents(self, archetype_id: str) -> list[AgentDef]:
        """Return all agents for an archetype, checking existence."""
        dna = self.get_dna(archetype_id)
        missing = [a for a in dna.agents if a.required and not a.exists]
        if missing:
            paths = [str(a.path) for a in missing]
            raise BmadAgentError(
                f"Required agents missing in '{archetype_id}': {paths}"
            )
        return list(dna.agents)
