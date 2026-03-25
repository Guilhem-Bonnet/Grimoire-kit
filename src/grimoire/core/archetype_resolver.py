"""Map detected stacks to archetype and agent selections."""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.core.scanner import ScanResult


@dataclass(frozen=True, slots=True)
class ResolvedArchetype:
    """Result of archetype resolution from a scan."""

    archetype: str
    stack_agents: tuple[str, ...]
    feature_agents: tuple[str, ...]
    reason: str
    # Multi-archetype support: ordered tuple of selected archetypes
    archetypes: tuple[str, ...] = ()

    @property
    def is_composite(self) -> bool:
        """True if more than one archetype was selected."""
        return len(self.archetypes) > 1


# Stack name → expert agent filename (without .md)
STACK_AGENT_MAP: dict[str, str] = {
    "go": "go-expert",
    "python": "python-expert",
    "javascript": "typescript-expert",
    "typescript": "typescript-expert",
    "docker": "docker-expert",
    "terraform": "terraform-expert",
    "kubernetes": "k8s-expert",
    "ansible": "ansible-expert",
}

# Known archetype IDs — keep in sync with cli/app.py _KNOWN_ARCHETYPES
_VALID_ARCHETYPES = frozenset({
    "minimal", "web-app", "creative-studio", "fix-loop",
    "infra-ops", "meta", "stack", "features", "platform-engineering",
})

# Archetype selection rules — evaluated top to bottom, first match wins.
# Each rule: (required_stacks, archetype_name, human_reason)
_ARCHETYPE_RULES: list[tuple[frozenset[str], str, str]] = [
    (frozenset({"terraform"}), "infra-ops", "Terraform detected"),
    (frozenset({"kubernetes"}), "infra-ops", "Kubernetes detected"),
    (frozenset({"ansible"}), "infra-ops", "Ansible detected"),
    (frozenset({"react", "python"}), "web-app", "React + Python backend"),
    (frozenset({"react", "go"}), "web-app", "React + Go backend"),
    (frozenset({"vue", "python"}), "web-app", "Vue + Python backend"),
    (frozenset({"vue", "go"}), "web-app", "Vue + Go backend"),
    (frozenset({"react"}), "web-app", "React frontend"),
    (frozenset({"vue"}), "web-app", "Vue frontend"),
    (frozenset({"django"}), "web-app", "Django web framework"),
    (frozenset({"fastapi"}), "web-app", "FastAPI service"),
]


class ArchetypeResolver:
    """Resolve a ScanResult into archetype + agent selections."""

    # Archetypes exposed for wizard display (exclude internal dirs)
    _USER_ARCHETYPES = ("minimal", "web-app", "infra-ops", "platform-engineering", "creative-studio", "fix-loop")

    def resolve(
        self,
        scan: ScanResult,
        *,
        backend: str = "local",
        archetype_override: str | None = None,
        archetypes_override: list[str] | None = None,
    ) -> ResolvedArchetype:
        detected = {d.name for d in scan.stacks}

        # Multi-archetype support
        if archetypes_override:
            invalid = [a for a in archetypes_override if a not in _VALID_ARCHETYPES]
            if invalid:
                msg = f"Unknown archetype(s): {', '.join(repr(a) for a in invalid)}"
                raise ValueError(msg)
            archetypes = tuple(archetypes_override)
            archetype = archetypes[0]  # primary for backward compat
            reason = f"User selected: {', '.join(archetypes)}"
        elif archetype_override:
            if archetype_override not in _VALID_ARCHETYPES:
                msg = f"Unknown archetype: {archetype_override!r}"
                raise ValueError(msg)
            archetype = archetype_override
            archetypes = (archetype_override,)
            reason = f"User selected: {archetype_override}"
        else:
            archetype = "minimal"
            archetypes = ("minimal",)
            reason = "No specific stack pattern matched"
            for required, arch, desc in _ARCHETYPE_RULES:
                if required <= detected:
                    archetype = arch
                    archetypes = (arch,)
                    reason = desc
                    break

        # Stack agents — deduplicated
        stack_agents: list[str] = []
        seen: set[str] = set()
        for stack_name in sorted(detected):
            agent = STACK_AGENT_MAP.get(stack_name)
            if agent and agent not in seen:
                stack_agents.append(agent)
                seen.add(agent)

        # Feature agents
        feature_agents: list[str] = []
        if backend in ("qdrant-local", "qdrant-server", "ollama"):
            feature_agents.append("vectus")

        return ResolvedArchetype(
            archetype=archetype,
            stack_agents=tuple(stack_agents),
            feature_agents=tuple(feature_agents),
            reason=reason,
            archetypes=archetypes,
        )

    def suggest_archetypes(self, scan: ScanResult) -> list[str]:
        """Suggest archetypes based on scan results (for guided discovery)."""
        detected = {d.name for d in scan.stacks}
        suggestions: list[str] = []
        seen: set[str] = set()
        for required, arch, _desc in _ARCHETYPE_RULES:
            if required <= detected and arch not in seen:
                suggestions.append(arch)
                seen.add(arch)
        return suggestions or ["minimal"]
